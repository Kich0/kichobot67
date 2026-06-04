import {UserDTO} from "./UserDTO.js";

export class GroupDTO {
    constructor(group) {
        this._id = group._id;
        this.name = group.name;
        this.url = group.url;
        this.owner = new UserDTO(group.owner);
        this.members = group.members.map(member => new UserDTO(member));
        this.admins = group.admins.map(admin => new UserDTO(admin));
        this.join_requests = group.join_requests.map(request => new UserDTO(request));

        this.createdAt = group.createdAt
        this.updatedAt = group.updatedAt
    }

    forRandom() {
        return {
            _id: this._id,
            name: this.name,
            url: this.url,
            owner: this.owner.mainInfo(),
            members: this.members.map(member => ({ _id: member._id }))
        }
    }
}

